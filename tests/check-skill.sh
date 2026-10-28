#!/usr/bin/env bash
# Validate full-stack-maintenance skill package: frontmatter, plugin manifests, links, JSON, and unit tests.
set -uo pipefail

root=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$root" || exit 1
failures=0
pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; failures=$((failures + 1)); }

frontmatter=$(awk 'NR == 1 && $0 == "---" { on = 1; next } on && $0 == "---" { exit } on' SKILL.md)
field() { printf '%s\n' "$frontmatter" | sed -n "s/^$1: //p"; }

echo "frontmatter"
name=$(field name)
[ "$name" = "full-stack-maintenance" ] && pass "name is full-stack-maintenance" || fail "name is '$name'"
printf '%s' "$name" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' && pass "name format" || fail "name format"
desc=$(field description)
[ -n "$desc" ] && [ ${#desc} -le 1024 ] && pass "description length ${#desc} <= 1024" || fail "description length ${#desc}"
compat=$(field compatibility)
[ -n "$compat" ] && [ ${#compat} -le 500 ] && pass "compatibility length ${#compat} <= 500" || fail "compatibility length ${#compat}"

version=$(printf '%s\n' "$frontmatter" | sed -n 's/^  version: "\(.*\)"$/\1/p')
[ -n "$version" ] && pass "metadata.version is a quoted string ($version)" || fail "metadata.version missing or unquoted"
changelog_version=$(sed -n 's/^## \([0-9][0-9.]*\).*/\1/p' CHANGELOG.md | head -n 1)
[ "$version" = "$changelog_version" ] && pass "CHANGELOG top entry matches $version" || fail "CHANGELOG top is '$changelog_version'"

allowed=$(field allowed-tools)
if printf '%s' "$allowed" | grep -Eq 'remove|prune|branch -|clean|push|rm |reset|stash|unlock|fetch|destroy'; then
  fail "allowed-tools pre-approves a mutating command: $allowed"
else
  pass "allowed-tools pre-approves read-only commands only"
fi

echo "Claude Code plugin"
json_get() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {'d': d}))" "$1" "$2" 2>/dev/null; }
[ "$(json_get .claude-plugin/plugin.json "d['name']")" = "$name" ] && pass "plugin.json name matches" || fail "plugin.json name"
[ "$(json_get .claude-plugin/plugin.json "d['version']")" = "$version" ] && pass "plugin.json version matches $version" || fail "plugin.json version"
[ "$(json_get .claude-plugin/marketplace.json "d['plugins'][0]['name']")" = "$name" ] && pass "marketplace entry name matches" || fail "marketplace entry name"
[ "$(json_get .claude-plugin/marketplace.json "d['plugins'][0]['source']")" = "." ] && pass "marketplace entry points at repository root" || fail "marketplace entry source"

claude_bin=${CLAUDE_BIN:-$(command -v claude || true)}
if [ -z "$claude_bin" ] && [ -x "$HOME/.local/bin/claude" ]; then
  claude_bin="$HOME/.local/bin/claude"
fi
if [ -n "$claude_bin" ]; then
  validation_tmp=$(mktemp -d "${TMPDIR:-/tmp}/full-stack-validate.XXXXXX") || exit 1
  trap 'rm -rf "$validation_tmp"' EXIT
  validation_root=$validation_tmp/full-stack-maintenance
  if python3 - "$root" "$validation_root" <<'PY'
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
shutil.copytree(source, sys.argv[2], symlinks=True,
                ignore=lambda path, names: ['.git', 'CLAUDE.md', 'work', 'topology.local.json'] if Path(path) == source or Path(path).name == 'config' else [])
PY
  then
    for target_pair in "$validation_root:marketplace" "$validation_root/.claude-plugin/plugin.json:plugin"; do
      target=${target_pair%:*}
      label=${target_pair#*:}
      if "$claude_bin" plugin validate --strict "$target" >"$validation_tmp/output.log" 2>&1; then
        pass "claude plugin validate --strict ($label)"
      else
        cat "$validation_tmp/output.log"
        fail "claude plugin validate --strict ($label)"
      fi
    done
  else
    fail "could not prepare plugin package for validation"
  fi
  rm -rf "$validation_tmp"
  trap - EXIT
else
  echo "  [SKIP] claude CLI not found"
fi

echo "size and references"
lines=$(awk 'END { print NR }' SKILL.md)
[ "$lines" -lt 500 ] && pass "SKILL.md has $lines lines (< 500)" || fail "SKILL.md has $lines lines"
for ref in references/*.md; do
  grep -q "($ref)" SKILL.md && pass "SKILL.md links $ref" || fail "SKILL.md does not link $ref"
done

echo "relative links"
for md in *.md references/*.md; do
  dir=$(dirname "$md")
  grep -o '\]([^)]*)' "$md" | sed 's/^](//; s/)$//' | while IFS= read -r target; do
    case $target in http*|mailto:*|file:*|\#*) continue ;; esac
    path=${target%%#*}
    [ -e "$dir/$path" ] || echo "$md -> $target"
  done
done >"${TMPDIR:-/tmp}/full-stack-links.$$"
broken=$(cat "${TMPDIR:-/tmp}/full-stack-links.$$"); rm -f "${TMPDIR:-/tmp}/full-stack-links.$$"
[ -z "$broken" ] && pass "all relative links resolve" || fail "broken links: $broken"

echo "JSON files"
for json in config/topology.json config/topology.example.json evals/evals.json evals/trigger-evals.json .claude-plugin/plugin.json .claude-plugin/marketplace.json; do
  python3 -m json.tool "$json" >/dev/null 2>&1 && pass "$json parses" || fail "$json does not parse"
done

echo
if [ "$failures" -eq 0 ]; then
  echo "check-skill: all checks passed"
else
  echo "check-skill: $failures check(s) failed"
  exit 1
fi
