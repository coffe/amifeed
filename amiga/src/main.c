/* main.c - amifeed: start up, pick the reader or a script command */
#include <exec/types.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <string.h>

#include "amifeed.h"

const char version[] = "$VER: amifeed " VERSION_STR " (20.10.2026)";

static void usage(void)
{
	PutStr("amifeed " VERSION_STR " - RSS reader (needs the amifeed proxy)\n"
	       "  amifeed                 the reader (in a console window)\n"
	       "  amifeed list [stream]   categories, or the unread articles of a stream\n"
	       "  amifeed read <id> [url] print an article and mark it read\n"
	       "  amifeed add <url> [LOCAL]  subscribe on the server, or LOCAL only here\n"
	       "  amifeed sync            save the read state of local feeds, show counts\n"
	       "Settings: ENVARC:amifeed/config (HOST=, PORT=, TOKEN=, WIDTH=, HEIGHT=, CHARSET=)\n");
}

int main(int argc, char **argv)
{
	int rc;

	if (argc > 1 && (!strcmp(argv[1], "?") || !strcmp(argv[1], "-h") || !strcmp(argv[1], "help"))) {
		usage();
		return RETURN_OK;
	}
	if (!load_config()) {
		PutStr("amifeed: ENVARC:amifeed/config needs at least HOST= and TOKEN=\n");
		return RETURN_FAIL;
	}
	if (!net_open()) {
		Printf("amifeed: %s\n", (LONG)err_msg);
		return RETURN_FAIL;
	}
	load_local_feeds();
	load_seen();

	if (argc < 2)
		rc = ui_main();
	else if (!strcmp(argv[1], "list"))
		rc = cmd_list(argc > 2 ? argv[2] : NULL);
	else if (!strcmp(argv[1], "read") && argc > 2)
		rc = cmd_read(argv[2], argc > 3 ? argv[3] : NULL);
	else if (!strcmp(argv[1], "add") && argc > 2)
		rc = cmd_add(argv[2], argc > 3 && (!strcmp(argv[3], "LOCAL") || !strcmp(argv[3], "local")));
	else if (!strcmp(argv[1], "sync"))
		rc = cmd_sync();
	else {
		usage();
		rc = RETURN_WARN;
	}
	save_seen(0);
	net_close();
	return rc;
}
