/* pxtest - ask the console for its size in pixels; answers go to T:pxtest.txt */
#include <exec/types.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <stdio.h>
#include <string.h>

static void ask(BPTR in, BPTR out, BPTR log, const char *q, const char *name)
{
	char b[64], line[256];
	int n = 0, i, k;
	UBYTE c;

	Write(out, (APTR)q, strlen(q));
	while (n < (int)sizeof(b) && WaitForChar(in, 500000) && Read(in, &c, 1) == 1)
		b[n++] = c;
	k = sprintf(line, "%s: %d bytes:", name, n);
	for (i = 0; i < n; i++)
		k += sprintf(line + k, (b[i] >= 32 && b[i] < 127) ? " %c" : " <%02x>", (UBYTE)b[i]);
	line[k++] = '\n';
	Write(log, line, k);
}

int main(void)
{
	BPTR in = Input(), out = Output(), log = Open("T:pxtest.txt", MODE_NEWFILE);

	if (!log)
		return 20;
	SetMode(in, 1);
	ask(in, out, log, "\x1b[14t", "14t text area px");
	ask(in, out, log, "\x1b[16t", "16t cell px");
	ask(in, out, log, "\x1b[18t", "18t chars");
	ask(in, out, log, "\x1b[?2;1;0S", "XTSMGRAPHICS sixel max");
	ask(in, out, log, "\x1b[?1;1;0S", "XTSMGRAPHICS colours");
	ask(in, out, log, "\x1b[c", "DA1");
	SetMode(in, 0);
	Close(log);
	return 0;
}
