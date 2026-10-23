#!/usr/bin/env bash
# Set up the job-hunt skill: checks what it needs, installs the Monid CLI, links the skill into your agents.
# Safe to rerun. Pass your Monid key as MONID_API_KEY to register it without typing it into a prompt.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
skill="$here/skills/job-hunt"
todo=0
if [ -t 1 ]; then g=$'\033[32m'; y=$'\033[33m'; r=$'\033[0m'; else g=""; y=""; r=""; fi
ok()   { printf '  %s✓%s %s\n' "$g" "$r" "$1"; }
need() { printf '  %s→%s %s\n' "$y" "$r" "$1"; todo=$((todo + 1)); }

echo "warm-apply setup (job-hunt skill)"

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
else
  need "Install Python 3.9 or newer (https://www.python.org/downloads/)"
fi

if command -v monid >/dev/null 2>&1; then
  ok "Monid CLI"
elif command -v npm >/dev/null 2>&1; then
  if npm install -g @monid-ai/cli@latest >/dev/null 2>&1; then ok "Monid CLI installed"; else need "Install the Monid CLI: npm install -g @monid-ai/cli"; fi
else
  need "Install Node.js (https://nodejs.org), then: npm install -g @monid-ai/cli"
fi

if command -v monid >/dev/null 2>&1; then
  if NO_COLOR=1 monid whoami >/dev/null 2>&1; then
    ok "Monid key"
  elif [ -n "${MONID_API_KEY:-}" ]; then
    NO_COLOR=1 monid keys add -k "$MONID_API_KEY" -l main >/dev/null 2>&1
    if NO_COLOR=1 monid whoami >/dev/null 2>&1; then ok "Monid key registered"; else need "Monid rejected MONID_API_KEY; create a new one at https://app.monid.ai/access/api-keys"; fi
  else
    need "Sign up for Monid at https://monid.ai?fpr=charles (\$1 free credit), create a key at https://app.monid.ai/access/api-keys, then: monid keys add -k YOUR_KEY -l main"
  fi
fi

chrome=""
for c in "/Applications/Google Chrome.app" "/Applications/Chromium.app" "/Applications/Microsoft Edge.app" "/Applications/Brave Browser.app"; do
  [ -d "$c" ] && chrome="$c"
done
for c in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do
  command -v "$c" >/dev/null 2>&1 && chrome="$c"
done
if [ -n "$chrome" ]; then ok "PDF rendering ($(basename "$chrome" .app))"; else need "Install Chrome, Chromium or Edge so resumes render to PDF"; fi

linked=0
for dir in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
  parent="$(dirname "$dir")"
  [ -d "$parent" ] || continue
  mkdir -p "$dir"
  if [ -e "$dir/job-hunt" ] && [ ! -L "$dir/job-hunt" ]; then
    need "$dir/job-hunt exists and isn't a link; remove it to install this version"
    continue
  fi
  ln -sfn "$skill" "$dir/job-hunt" && ok "Linked into $dir" && linked=1
done
[ "$linked" = 1 ] || need "No agent skills folder found. Copy $skill into your agent's skills folder (~/.claude/skills for Claude Code, ~/.agents/skills for Codex, Cursor, Gemini CLI)"

if (cd "$skill/scripts" && JOBHUNT_OFFLINE=1 python3 -m unittest -q test_jobhunt.py >/dev/null 2>&1); then ok "Self-test passed"; else need "Self-test failed: cd $skill/scripts && python3 -m unittest test_jobhunt.py"; fi

echo
if [ "$todo" = 0 ]; then
  echo "Ready. Open your agent (Claude Code, Codex, Cursor, Gemini CLI) anywhere and say:"
  echo "  \"Set up my job hunt\" (then attach or paste your resume)"
  echo "Your files will live in ~/job-search/job-hunt (set JOB_HUNT_HOME to use another folder)."
else
  echo "$todo thing(s) to finish above, then rerun ./install.sh"
fi
