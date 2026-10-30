#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only (this folder modifies AGPL-3.0 code, see LICENSE and LICENSE-NOTICE.md here)
# Build signal-call-tunnel for voice calls: pinned sources + local.patch, as the normal user, never with sudo.
# Needs (Homebrew): rustup cmake protobuf coreutils; then: rustup toolchain install 1.91.1 --profile minimal
# Usage: tools/call-tunnel/build.sh [target dir]
#   default: $LOCAL_ASSISTANT_VENDOR/signal-call-tunnel, with LOCAL_ASSISTANT_VENDOR defaulting to
#   this checkout's vendor/ (gitignored; estia-deploy copies it into her release)
set -eu

TUNNEL_REPO=https://github.com/visigoth/signal-call-tunnel
PR5_HEAD=2f924268e1d3083eef70dd82718f1e7e760b13d1   # PR #5 pipe audio, on upstream main 02c84e9
RINGRTC=a86f8a6832cec291ef4f77e4ee9b941e5b91a01f     # submodule pin (RingRTC v2.65.1)
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$(dirname "$0")/../.." && pwd)
VENDOR="${LOCAL_ASSISTANT_VENDOR:-$REPO/vendor}"
DEST=${1:-$VENDOR/signal-call-tunnel}
BREW=$(brew --prefix 2>/dev/null || echo /opt/homebrew)  # Apple silicon default when brew is not on PATH

if [ ! -d "$DEST/.git" ]; then
  mkdir -p "$(dirname "$DEST")"
  git clone -q "$TUNNEL_REPO" "$DEST"
fi
BIN="$DEST/signal-call-tunnel/target/release/signal-call-tunnel"
# what this helper is built from, written beside it once built: setup and doctor compare it with the checkout's or
# her release's own sources, so a later Estia's local.patch builds it again (second deep scan, signal-06)
PATCH_SHA=$(shasum -a 256 "$HERE/local.patch" | cut -d ' ' -f 1)
rm -f "$BIN.stamp"
cd "$DEST"
git fetch -q origin "$PR5_HEAD"
git reset -q --hard  # its own clone: an older local.patch applied before is undone first
git checkout -q --detach "$PR5_HEAD"
git submodule update --init third-party/ringrtc >/dev/null
[ "$(git -C third-party/ringrtc rev-parse HEAD)" = "$RINGRTC" ] || { echo "unexpected RingRTC commit" >&2; exit 1; }
if git apply --check "$HERE/local.patch" 2>/dev/null; then
  git apply "$HERE/local.patch"
else
  echo "local.patch does not apply: is the tree clean?" >&2
  exit 1
fi
env -u OUTPUT_DIR RUSTUP_TOOLCHAIN=1.91.1 \
  PATH="$BREW/opt/rustup/bin:$BREW/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  ./build.sh build --locked
printf 'pr5_head=%s\nringrtc=%s\nlocal_patch=%s\n' "$PR5_HEAD" "$RINGRTC" "$PATCH_SHA" > "$BIN.stamp"
echo "built: $BIN"
shasum -a 256 "$BIN"
