#!/usr/bin/env bash
# Build debugfs and e2fsck from the pinned upstream e2fsprogs source, for the release zips (Linux and macOS),
# so nobody has to install e2fsprogs. Linux: fully static. macOS: e2fsprogs' own libraries built in, only the
# system's libSystem used. e2fsprogs is GPL-2.0: its licence and the source's address go in the same folder,
# and the release attaches the source tarball itself.
#   usage: scripts/build-e2fsprogs.sh <dest folder>
set -euo pipefail
VERSION=1.47.4
SHA256=fd5bf388cbdbe006a3d3b318d983b2948382440acc85a87f1e7d108653e8db0b
URL=https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v$VERSION/e2fsprogs-$VERSION.tar.xz
dest=$(mkdir -p "$1" && cd "$1" && pwd)
work=$(mktemp -d)
cd "$work"
curl -sSfL -o src.tar.xz "$URL"
echo "$SHA256  src.tar.xz" | shasum -a 256 -c -
tar -xJf src.tar.xz
cd "e2fsprogs-$VERSION"
flags=(--disable-nls --disable-fuse2fs --disable-e2initrd-helper --disable-uuidd --without-libarchive
       --disable-defrag --disable-imager)
if [ "$(uname)" = "Linux" ]; then
  # Baseline x86-64 on purpose: a toolchain that defaults to newer (x86-64-v3) made a debugfs that needs AVX2.
  ./configure "${flags[@]}" CFLAGS="${CFLAGS:--O2} -march=x86-64 -mtune=generic" LDFLAGS=-static >/dev/null
else
  ./configure "${flags[@]}" >/dev/null
fi
jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
make -j"$jobs" libs >/dev/null
make -j"$jobs" -C debugfs debugfs >/dev/null
make -j"$jobs" -C e2fsck e2fsck >/dev/null
cp debugfs/debugfs e2fsck/e2fsck "$dest/"
cp NOTICE "$dest/NOTICE"
cat > "$dest/SOURCE.txt" <<TXT
debugfs and e2fsck from e2fsprogs $VERSION (GPL-2.0, see NOTICE), built unchanged from:
  $URL
  sha256 $SHA256
The same source is attached to this release. Build script: scripts/build-e2fsprogs.sh in
https://github.com/TRusselo/lenovo-clock-ha-kiosk
TXT
"$dest/debugfs" -V
if [ "$(uname)" = "Darwin" ]; then otool -L "$dest/debugfs" "$dest/e2fsck"; fi
