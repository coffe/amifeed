#!/bin/sh
# Build amifeed-<version>.lha for a GitHub release: binary, icons, docs.
#   amiga/release/release.sh [OUTDIR]   (needs Docker; default OUTDIR is amiga/release/out)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
amiga=$(dirname "$here"); repo=$(dirname "$amiga")
out=${1:-$here/out}
ver=$(sed -n 's/^#define VERSION_STR *"\(.*\)"/\1/p' "$amiga/src/amifeed.h")
make -C "$amiga" -s
stage=$(mktemp -d); trap 'rm -r "$stage"' EXIT
mkdir -p "$stage/amifeed" "$out"
cp "$amiga/amifeed" "$here/INSTALL.txt" "$here/config.example" "$stage/amifeed/"
cp "$repo/README.md" "$stage/amifeed/README.txt"
cp "$repo/LICENSE" "$stage/amifeed/LICENSE.txt"
python3 "$here/mkicons.py" "$stage"
docker run --rm -v "$stage:/w" -w /w debian:bookworm-slim sh -euc "
    apt-get update -qq >/dev/null && apt-get install -y -qq jlha-utils >/dev/null 2>&1
    lha -aqo5 amifeed-$ver.lha amifeed.info amifeed
    chown $(id -u):$(id -g) amifeed-$ver.lha"
mv "$stage/amifeed-$ver.lha" "$out/"
echo "$out/amifeed-$ver.lha"
