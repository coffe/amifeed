# Builds amifeed for AmigaOS 3.x (68020+) inside the walkero vbcc image.
#   make          - build ./amifeed
#   make clean
IMAGE  ?= walkero/docker4amigavbcc:latest-m68k
SDK    := /opt/sdk
CFLAGS := +aos68k -cpu=68020 -O2 -c99 -I$(SDK)/NDK3.2/Include_H -I$(SDK)/Roadshow-SDK/netinclude
SRC    := $(wildcard src/*.c)

amifeed: $(SRC) src/amifeed.h
	docker run --rm -u $$(id -u):$$(id -g) -v $(CURDIR):/w -w /w $(IMAGE) \
		vc $(CFLAGS) $(SRC) -o amifeed -lamiga

clean:
	rm -f amifeed

.PHONY: clean
