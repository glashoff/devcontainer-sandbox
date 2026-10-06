#!/bin/sh
# Fetches the two KiCad AppImage archives this image is built from, into the
# directory of this script, and checks them against sha256sums.
#
# They are kept locally instead of downloaded during the build: the nightly is
# pinned by name, those names disappear from the server after a while, and the
# build of the image every project uses must not depend on that. Run this once
# per clone; the archives are not in git (981 MB, and GitHub refuses files
# over 100 MB).
set -eu
cd "$(dirname "$0")"
base=https://downloads.kicad.org/kicad/linux/explore
for file in $(awk '{print $2}' sha256sums); do
    case "$file" in
        *nightly*) path="nightlies/download/$file" ;;
        *)         path="stable/download/$file" ;;
    esac
    [ -f "$file" ] || curl -fL --progress-bar -o "$file" "$base/$path"
    # The .minisig beside it is kept for the record. KiCad publishes no
    # public key that could check it, so sha256sums is what we have: it says
    # the file has not changed since it was first fetched, not that it is
    # genuine.
    [ -f "$file.minisig" ] || curl -fsSL -o "$file.minisig" "$base/$path.minisig" || true
done
sha256sum -c sha256sums
