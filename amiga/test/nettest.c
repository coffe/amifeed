/*
 * nettest - step 0 smoke test for amifeed on a 68020 Amiga.
 *
 *   nettest <ip> <port>      HTTP/1.0 GET / against <ip>:<port>, prints the reply
 *   nettest KEYS             console test: colours, Latin-1, window size, raw keys
 *
 * Built with vbcc (+aos68k, -cpu=68020), bsdsocket.library from Roadshow.
 */
#include <exec/types.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/bsdsocket.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <string.h>
#include <stdlib.h>

struct Library *SocketBase;

static char buf[1024];

static int net_test(const char *ip, const char *port)
{
	struct sockaddr_in sa;
	struct timeval tv;
	LONG s, n, total = 0;
	char req[128];

	SocketBase = OpenLibrary("bsdsocket.library", 4);
	if (!SocketBase) {
		Printf("No bsdsocket.library - is Roadshow running?\n");
		return RETURN_FAIL;
	}
	s = socket(AF_INET, SOCK_STREAM, 0);
	if (s < 0) {
		Printf("socket() failed, errno %ld\n", Errno());
		CloseLibrary(SocketBase);
		return RETURN_FAIL;
	}
	tv.tv_sec = 10;
	tv.tv_usec = 0;
	setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

	memset(&sa, 0, sizeof(sa));
	sa.sin_family = AF_INET;
	sa.sin_port = htons((UWORD)atoi(port));
	sa.sin_addr.s_addr = inet_addr((STRPTR)ip);
	if (connect(s, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
		Printf("connect() failed, errno %ld\n", Errno());
		CloseSocket(s);
		CloseLibrary(SocketBase);
		return RETURN_FAIL;
	}
	strcpy(req, "GET / HTTP/1.0\r\nConnection: close\r\n\r\n");
	send(s, req, strlen(req), 0);
	while ((n = recv(s, buf, sizeof(buf) - 1, 0)) > 0) {
		buf[n] = 0;
		PutStr(buf);
		total += n;
	}
	if (n < 0)
		Printf("\nrecv() failed, errno %ld\n", Errno());
	Printf("\n-- %ld bytes\n", total);
	CloseSocket(s);
	CloseLibrary(SocketBase);
	return RETURN_OK;
}

/* Prints whatever the console answers within ~1 s, as hex. */
static void dump_reply(BPTR in, const char *what)
{
	LONG n = 0;
	UBYTE c;

	Printf("%s reply:", what);
	while (n < 40 && WaitForChar(in, 1000000)) {
		if (Read(in, &c, 1) != 1)
			break;
		Printf(" %02lx", (ULONG)c);
		n++;
	}
	Printf(n ? "\n" : " (none)\n");
}

static int key_test(void)
{
	BPTR in = Input(), out = Output();
	UBYTE c;

	PutStr("\x1b[31mred\x1b[0m \x1b[32mgreen\x1b[0m \x1b[1;34mbold blue\x1b[0m\n");
	PutStr("Latin-1: \xe5\xe4\xf6 \xc5\xc4\xd6 \xe9\n");
	if (!SetMode(in, 1)) {
		PutStr("SetMode(RAW) failed - not a console?\n");
		return RETURN_WARN;
	}
	Write(out, "\x9b" "0 q", 4);
	dump_reply(in, "Amiga window status (CSI 0 q)");
	Write(out, "\x1b[18t", 5);
	dump_reply(in, "xterm size (ESC[18t)");

	PutStr("Press keys (hex shown), q quits\n");
	while (Read(in, &c, 1) == 1) {
		Printf(" %02lx", (ULONG)c);
		Flush(out);
		if (c == 'q')
			break;
	}
	SetMode(in, 0);
	PutStr("\n");
	return RETURN_OK;
}

int main(int argc, char **argv)
{
	if (argc == 3)
		return net_test(argv[1], argv[2]);
	if (argc == 2 && !strcmp(argv[1], "KEYS"))
		return key_test();
	PutStr("usage: nettest <ip> <port> | nettest KEYS\n");
	return RETURN_WARN;
}
