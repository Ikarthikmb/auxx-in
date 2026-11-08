#!/usr/bin/env bash
# Fails if anything private would be published: run it before every commit / push
# (CI runs it too). Checks the files git would publish (or, outside a git
# checkout, everything not git-ignored by the patterns below).
#
# Your own private words (server IP, hostnames, usernames...) go one per line in
# .check-public.local, which is git-ignored, so the list itself stays private.
set -euo pipefail
cd "$(dirname "$0")/.."

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t files < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t files < <(find . -type f -not -path './.git/*' -not -path './data/*' -not -path './music/*' -not -path '*/__pycache__/*' \
    -not -name '.env' -not -name '.check-public.local' -not -path './.pytest_cache/*' -not -path './.ruff_cache/*' | sed 's|^\./||')
fi

fail=0
say() { echo "check-public: $*" >&2; fail=1; }

# 1. Files that must never be published.
for f in "${files[@]}"; do
  case "$f" in
    data/.gitkeep|music/.gitkeep) ;;  # empty folders, so Docker doesn't make them as root
    music/*) say "music would be published: $f" ;;
    .env|*/.env|data/*|*.key|*.pem|cookies.txt|*/cookies.txt|users.json|config.json|state.json|.check-public.local)
      say "private file would be published: $f" ;;
  esac
done

text=()
for f in "${files[@]}"; do
  [ -f "$f" ] && [ "$f" != "scripts/check-public.sh" ] && grep -Iq . "$f" 2>/dev/null && text+=("$f")
done
[ ${#text[@]} -eq 0 ] && { echo "check-public: nothing to check"; exit $fail; }

scan() {  # label, grep -E pattern
  local hits
  if hits=$(grep -nIE -- "$2" "${text[@]}" 2>/dev/null); then
    say "$1:"; echo "$hits" | head -20 >&2
  fi
}
# 2. Private network addresses (LAN, Tailscale) and private hostnames.
scan "private IP address" '\b(10\.[0-9]{1,3}|192\.168|172\.(1[6-9]|2[0-9]|3[01])|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7]))\.[0-9]{1,3}(\.[0-9]{1,3})?\b'
scan "private hostname" '[A-Za-z0-9-]+\.(ts\.net|internal|lan|home\.arpa)\b'
scan "home folder path" '/home/[a-z][a-z0-9_-]*/|/Users/[A-Za-z][A-Za-z0-9_-]*/'
# 3. Things that look like secrets.
scan "secret-looking value" '(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
scan "filled-in secret in an env file" '^(NAVIDROME_PASS|LLM_KEY|INTERNAL_KEY|SECRET_KEY)=[^[:space:]#]+'

# 4. Your own private words.
if [ -f .check-public.local ]; then
  while IFS= read -r w || [ -n "$w" ]; do
    w="${w%%#*}"; w="$(echo "$w" | xargs)"; [ -z "$w" ] && continue
    if hits=$(grep -nIiwF -- "$w" "${text[@]}" 2>/dev/null); then
      say "private word from .check-public.local:"; echo "$hits" | cut -c1-160 | head -10 >&2
    fi
  done < .check-public.local
fi

[ $fail -eq 0 ] && echo "check-public: OK (${#files[@]} files)"
exit $fail
