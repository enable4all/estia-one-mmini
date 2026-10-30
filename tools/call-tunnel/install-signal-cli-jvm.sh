#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only (this folder modifies AGPL-3.0 code, see LICENSE and LICENSE-NOTICE.md here)
# Install the JVM build of signal-cli (Homebrew's native build cannot place calls: it lacks reflection metadata for
# TurnServerInfo). The version must match the Homebrew one (same account data). The release, its signature and the
# maintainer's key are public downloads from GitHub (no GitHub account, no gh), fetched with /usr/bin/curl at the
# pinned addresses below. A version with a pinned sha256 must match it; the signature is checked against the
# maintainer's key, and both that key and the signature's signer must have the fingerprint pinned below, or nothing
# is unpacked. Needs: brew install openjdk gnupg.
# Usage: tools/call-tunnel/install-signal-cli-jvm.sh [version] [target dir]
#   default target: $LOCAL_ASSISTANT_VENDOR/signal-cli-<version>-jvm, with LOCAL_ASSISTANT_VENDOR defaulting to
#   this checkout's vendor/ (gitignored; estia-deploy copies it into her release)
set -eu

# AsamK's signal-cli release key (signal-cli's README and the keyservers name it); a key GitHub serves with any other
# fingerprint, or a signature by any other key, stops the install before anything is unpacked
FINGERPRINT=FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570

VERSION=${1:-0.14.9}
# the version goes into the download's address: three or four numbers with dots between, nothing else (no newline
# either); signal-cli has shipped a four-part one (0.14.4.1)
case $VERSION in
  *[!0-9.]* | *..* | .* | *. | *.*.*.*.*) VALID= ;;
  *.*.*.* | *.*.*) VALID=yes ;;
  *) VALID= ;;
esac
[ -n "$VALID" ] \
  || { echo "the version must be three or four numbers, such as 0.14.9 (Homebrew's: signal-cli --version)" >&2; exit 2; }
# each release tarball's sha256, taken from a download whose signature checked out against the pinned key (and
# GitHub's own asset digest): a pinned version must match it too. Homebrew's signal-cli may be newer than the last
# pin: such a version is checked by its signature alone, and the install says so
case $VERSION in
  0.14.9) SHA256=c32b87f587198cbd277c9d71228716ee6047ee2b63e8e88728e8280d9407d65a ;;
  *) SHA256= ;;
esac
RELEASE_URL=https://github.com/AsamK/signal-cli/releases/download/v$VERSION/signal-cli-$VERSION.tar.gz
KEY_URL=https://github.com/AsamK.gpg  # the maintainer's public keys, as GitHub serves them to anyone
# the Mac's own curl by its full path (a test points it at a stub with ESTIA_TEST_ROOT)
CURL="${ESTIA_TEST_ROOT:-}/usr/bin/curl"
REPO=$(cd "$(dirname "$0")/../.." && pwd)
VENDOR="${LOCAL_ASSISTANT_VENDOR:-$REPO/vendor}"
DEST=${2:-$VENDOR/signal-cli-$VERSION-jvm}
BREW=$(brew --prefix 2>/dev/null || echo /opt/homebrew)  # Apple silicon default when brew is not on PATH
GPG=$(command -v gpg || echo "$BREW/bin/gpg")
[ -e "$DEST" ] && { echo "$DEST exists: installed already (to install it again, delete that folder first)" >&2; exit 1; }
# what it needs, checked before anything is downloaded or made
[ -x "$GPG" ] || { echo "gpg is missing: brew install gnupg" >&2; exit 1; }
JAVA="${JAVA_HOME:-$BREW/opt/openjdk}"
[ -x "$JAVA/bin/java" ] || { echo "Java is missing: brew install openjdk" >&2; exit 1; }

# fetch <address> <file> <most bytes> <most seconds>: HTTPS only, redirects too (GitHub sends a release on to its file
# host), never ~/.curlrc (-q, first), an HTTP error is a failure (-f); a stalled line (under 10 KB/s for a minute), a
# file far larger than expected or a download over its time ends it. A failure stops the install.
fetch() {
  "$CURL" -q -fsSL --proto '=https' --proto-redir '=https' --retry 3 --connect-timeout 20 --speed-limit 10240 \
    --speed-time 60 --max-time "$4" --max-filesize "$3" -o "$2" "$1" \
    || { echo "download failed: $1 (curl's exit code $?); nothing was installed" >&2; exit 1; }
}

# everything happens in a temporary folder, moved into place only once the checks pass: a failed run leaves nothing
# behind, so running it again just works
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
GNUPGHOME="$WORK/gnupg"
mkdir -m 700 "$GNUPGHOME" "$WORK/dl"
export GNUPGHOME
cd "$WORK/dl"
echo "downloading signal-cli $VERSION (about 120 MB) from GitHub: $RELEASE_URL"
fetch "$RELEASE_URL" "signal-cli-$VERSION.tar.gz" 300000000 3600
fetch "$RELEASE_URL.asc" "signal-cli-$VERSION.tar.gz.asc" 100000 60
fetch "$KEY_URL" maintainer.asc 1000000 60
if [ -n "$SHA256" ]; then
  got=$(/usr/bin/shasum -a 256 "signal-cli-$VERSION.tar.gz" | /usr/bin/awk '{ print $1 }')
  [ "$got" = "$SHA256" ] || {
    echo "the download's sha256 is $got, not the pinned $SHA256: nothing was installed" >&2
    exit 1
  }
else
  echo "no sha256 pin for signal-cli $VERSION in this script: it is checked by its signature alone" >&2
fi
"$GPG" --quiet --import maintainer.asc
# the imported primary keys' fingerprints (pub, then its fpr line): the pinned one must be among them
"$GPG" --with-colons --fingerprint | awk -F: '$1 == "pub" { want = 1; next } $1 == "fpr" && want { print $10; want = 0 }' \
  | grep -qx "$FINGERPRINT" || { echo "the maintainer's key does not have the pinned fingerprint $FINGERPRINT" >&2; exit 1; }
# the signature must be good (exit status) and made by the pinned key: VALIDSIG names the signing key and, last, its
# primary key (a subkey's signature counts by its primary key)
"$GPG" --status-fd 1 --verify "signal-cli-$VERSION.tar.gz.asc" "signal-cli-$VERSION.tar.gz" > verify.status
awk '$2 == "VALIDSIG" { print $3; if (NF >= 12) print $NF }' verify.status | grep -qx "$FINGERPRINT" \
  || { echo "the signature is not by the key with the pinned fingerprint $FINGERPRINT" >&2; exit 1; }
mkdir "$WORK/out"
tar -xzf "signal-cli-$VERSION.tar.gz" -C "$WORK/out"
JAVA_HOME="$JAVA" "$WORK/out/signal-cli-$VERSION/bin/signal-cli" --version
mkdir -p "$(dirname "$DEST")"
mv "$WORK/out" "$DEST"
echo "installed: $DEST/signal-cli-$VERSION/bin/signal-cli"
