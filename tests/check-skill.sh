#!/usr/bin/env bash
# Validate full-stack-maintenance skill package: frontmatter, host manifests, docs, syntax, JSON, and privacy guards.
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
if printf '%s' "$allowed" | grep -Eq 'quarantine|remove|prune|branch -|clean|push|rm |reset|stash|unlock|fetch|destroy'; then
  fail "allowed-tools pre-approves a mutating command: $allowed"
else
  pass "allowed-tools pre-approves read-only commands only"
fi

echo "OpenAI / Codex agent manifest"
if [ -s agents/openai.yaml ]; then
  if openai_err=$(python3 -B - agents/openai.yaml 2>&1 <<'PY'
import re
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
in_interface = False
fields: dict[str, str] = {}

for raw in lines:
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if not raw.startswith((" ", "\t")):
        in_interface = stripped == "interface:"
        continue
    if in_interface:
        m = re.match(r"^\s+([a-zA-Z0-9_]+)\s*:\s*(.+?)\s*$", raw)
        if m:
            key, val = m.group(1), m.group(2)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            fields[key] = val.strip()

missing = [k for k in ("display_name", "short_description", "default_prompt") if not fields.get(k)]
if missing:
    sys.exit(f"missing or empty interface field(s): {', '.join(missing)}")
if "$full-stack-maintenance" not in fields["default_prompt"]:
    sys.exit("interface.default_prompt must reference $full-stack-maintenance")
PY
  ); then
    pass "agents/openai.yaml interface fields valid and reference \$full-stack-maintenance"
  else
    fail "agents/openai.yaml invalid ($openai_err)"
  fi
else
  fail "agents/openai.yaml missing or empty"
fi

echo "Claude Code plugin"
json_get() { python3 -B -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); print(eval(sys.argv[2], {'d': d}))" "$1" "$2" 2>/dev/null; }
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
  if python3 -B - "$root" "$validation_root" <<'PY'
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
root_ignored = {'.git', 'CLAUDE.md', 'state', '.worktrees', '.venv'}
always_ignored = {'__pycache__', '.DS_Store'}


def _ignore(path: str, names: list[str]) -> list[str]:
    current = Path(path)
    ignored = set(always_ignored)
    if current == source:
        ignored |= root_ignored
    if current.name == 'config':
        ignored.add('topology.local.json')
    return [n for n in names if n in ignored or n.endswith(('.pyc', '.pyo', '.pyd'))]


shutil.copytree(source, sys.argv[2], symlinks=True, ignore=_ignore)
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

echo "required docs, size, and references"
for doc in README.md README.zh-CN.md README.zh-TW.md README.ja.md REVIEW.md; do
  if [ -f "$doc" ] && [ -n "$(tr -d '[:space:]' <"$doc")" ]; then
    pass "$doc exists and is non-empty"
  else
    fail "$doc missing or empty"
  fi
done

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
    [ -z "$path" ] && continue
    [ -e "$dir/$path" ] || echo "$md -> $target"
  done
done >"${TMPDIR:-/tmp}/full-stack-links.$$"
broken=$(cat "${TMPDIR:-/tmp}/full-stack-links.$$"); rm -f "${TMPDIR:-/tmp}/full-stack-links.$$"
[ -z "$broken" ] && pass "all relative links resolve" || fail "broken links: $broken"

echo "Python bytecode syntax"
pyc_tmp=$(mktemp -d "${TMPDIR:-/tmp}/full-stack-pyc.XXXXXX") || exit 1
if PYTHONPYCACHEPREFIX="$pyc_tmp" python3 -B -m py_compile scripts/*.py >/dev/null 2>&1 &&
   python3 -B -c 'import pathlib; [compile(p.read_bytes(), str(p), "exec") for d in ("scripts", "tests") for p in sorted(pathlib.Path(d).rglob("*.py"))]' >/dev/null 2>&1; then
  pass "python3 -B -m py_compile scripts/*.py (and recursive syntax check)"
else
  fail "python3 -B -m py_compile scripts/*.py failed"
fi
rm -rf "$pyc_tmp"

echo "JSON files"
for json in config/topology.json config/topology.example.json evals/evals.json evals/trigger-evals.json .claude-plugin/plugin.json .claude-plugin/marketplace.json; do
  python3 -B -m json.tool "$json" >/dev/null 2>&1 && pass "$json parses" || fail "$json does not parse"
done

echo "privacy and local-config guard"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git ls-files --error-unmatch config/topology.local.json >/dev/null 2>&1; then
    fail "config/topology.local.json must never be tracked by git"
  else
    pass "config/topology.local.json is not tracked"
  fi
fi

if privacy_err=$(python3 -B - "$root" 2>&1 <<'PY'
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2"}

proc = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    cwd=root,
    capture_output=True,
    check=False,
)
if proc.returncode == 0 and proc.stdout:
    raw_list = [p.decode("utf-8", errors="surrogateescape") for p in proc.stdout.split(b"\0") if p]
    rel_paths = list(dict.fromkeys(raw_list))
else:
    skip_top = {".git", "state", ".worktrees", ".venv", "__pycache__"}
    rel_paths = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[0] in skip_top or "__pycache__" in rel.parts or rel.name == ".DS_Store":
            continue
        if rel.as_posix() == "config/topology.local.json":
            continue
        rel_paths.append(rel.as_posix())

HOME_USER_RE = re.compile(r"/Users/([A-Za-z0-9._-]+)")
LINUX_HOME_RE = re.compile(r"(?:^|[\s\"'\x60=:(\[{,;|>/])/home/([A-Za-z0-9][A-Za-z0-9._-]*)")
ALLOWED_HOME_USERS = re.compile(
    r"^(?:"
    r"shared|public|default|guest|user|username|name|someone|somebody|"
    r"example|demo|test|tester|fixtures?|mock|sample|dummy|placeholder|"
    r"runner|runneradmin|ubuntu|debian|ec2-user|nobody"
    r")$",
    re.IGNORECASE,
)

PRIVATE_IP_RE = re.compile(
    r"(?<![0-9.])("
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r")(?![0-9.])"
)

TAILNET_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+ts\.net)\b", re.IGNORECASE)
ALLOWED_TAILNET = re.compile(r"^(?:example|tailnet(?:-name)?|your-[a-z0-9-]+|my-tailnet)\.ts\.net$", re.IGNORECASE)

violations: list[str] = []
for rel in rel_paths:
    if rel == "config/topology.local.json":
        violations.append(f"{rel}: local topology file must not be tracked")
        continue
    fpath = root / rel
    if fpath.suffix.lower() in BINARY_SUFFIXES or not fpath.is_file():
        continue
    try:
        text = fpath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in HOME_USER_RE.finditer(line):
            token = m.group(1)
            if not ALLOWED_HOME_USERS.match(token):
                violations.append(f"{rel}:{lineno}: disallowed home directory path '/Users/{token}'")
        for m in LINUX_HOME_RE.finditer(line):
            token = m.group(1)
            if not ALLOWED_HOME_USERS.match(token):
                violations.append(f"{rel}:{lineno}: disallowed home directory path '/home/{token}'")
        for m in PRIVATE_IP_RE.finditer(line):
            ip = m.group(1)
            parts = [int(x) for x in ip.split(".")]
            if all(0 <= p <= 255 for p in parts):
                violations.append(f"{rel}:{lineno}: disallowed private/CGNAT IP '{ip}' (use RFC 5737 192.0.2.x)")
        for m in TAILNET_RE.finditer(line):
            host = m.group(1)
            if not ALLOWED_TAILNET.match(host):
                violations.append(f"{rel}:{lineno}: disallowed tailnet hostname '{host}'")

if violations:
    sys.exit("\n".join(violations[:10]))
PY
); then
  pass "no personal home paths or private IPs in tracked files"
else
  fail "privacy guard triggered: $privacy_err"
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "check-skill: all checks passed"
else
  echo "check-skill: $failures check(s) failed"
  exit 1
fi
