#!/usr/bin/env bash
# Known security holes in every locked Python package (uv.lock, and each helper's tools/*/uv.lock), checked with
# osv-scanner (Homebrew's) against the public OSV database, offline: no package name leaves the Mac.
#   scripts/vuln-scan.sh             offline, against the database downloaded before
#   scripts/vuln-scan.sh --download  downloads a fresh database first (the release steps: docs/agents/docs-release.md)
#   scripts/vuln-scan.sh --if-ready  as the first, or one "not checked" line and success without osv-scanner or a
#                                    database (scripts/verify.sh)
# The database is kept in ~/.cache/osv-scanner; the pass line names its day, and a line warns when it is over 3 days
# old. Exit 0: no known hole; 1: a known hole (a release with one is not published); 2: no osv-scanner, no database
# yet, or the scanner failed.
set -euo pipefail
cd "$(dirname "$0")/.."
homebrew="${ESTIA_TEST_ROOT:-}/opt/homebrew"  # Homebrew's folder on Apple silicon (a test: its fake tree)
scanner="$homebrew/bin/osv-scanner"
export OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY="$HOME/.cache/osv-scanner"

mode="${1:-offline}"
case "$mode" in
  offline | --download | --if-ready) ;;
  *) echo "usage: scripts/vuln-scan.sh [--download | --if-ready]" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo "usage: scripts/vuln-scan.sh [--download | --if-ready]" >&2; exit 2; }

not_ready() {
  if [[ "$mode" == --if-ready ]]; then
    echo "○ Known security holes: not checked ($1)"
    exit 0
  fi
  echo "✗ $1"
  exit 2
}

[[ -x "$scanner" ]] || not_ready "osv-scanner is not installed: brew install osv-scanner"
shopt -s nullglob
databases=("$OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"/*/PyPI/all.zip)
if [[ "$mode" != --download && ${#databases[@]} -eq 0 ]]; then
  not_ready "no offline database yet: scripts/vuln-scan.sh --download"
fi

locks=(uv.lock tools/*/uv.lock)
args=()
for lock in "${locks[@]}"; do
  args+=(-L "$lock")
done
if [[ "$mode" == --download ]]; then
  network=(--offline-vulnerabilities --download-offline-databases)  # the download is its only network use
else
  network=(--offline --offline-vulnerabilities)
fi

status=0
"$scanner" scan source "${network[@]}" "${args[@]}" || status=$?
case "$status" in
  0)
    # the database's day, and its age past max_days: a pass against an old one says so (review X6, 0.9.7)
    max_days=3
    newest="" newest_s=0
    for database in "$OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"/*/PyPI/all.zip; do
      at=$(date -r "$database" +%s)
      if (( at > newest_s )); then newest="$database" newest_s=$at; fi
    done
    day="of an unknown day"
    [[ -n "$newest" ]] && day="of $(date -r "$newest" +%Y-%m-%d)"
    echo "✓ No known security hole in the ${#locks[@]} locked package lists (the OSV database $day, offline)"
    age=$(( ($(date +%s) - newest_s) / 86400 ))
    if [[ -n "$newest" ]] && (( age > max_days )); then
      echo "! The OSV database is $age days old: scripts/vuln-scan.sh --download for a fresh one (before a release, always)"
    fi
    ;;
  1)
    echo "✗ A known security hole, above: update that package to its fixed version (SECURITY.md, Vulnerability"
    echo "  management); a release with one is not published."
    exit 1
    ;;
  *) echo "✗ osv-scanner failed (exit $status)"; exit 2 ;;
esac
