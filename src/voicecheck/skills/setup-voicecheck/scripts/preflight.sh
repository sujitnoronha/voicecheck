#!/usr/bin/env bash
# VoiceCheck setup preflight. Reports environment readiness; mutates nothing.
# Exit 0 if Python is new enough, 1 otherwise.
set -u

py=""
for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { py="$c"; break; }; done
if [ -z "$py" ]; then
  echo "python: NOT FOUND — install Python 3.10+"
  exit 1
fi

ver=$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
echo "python: $ver ($py)"
"$py" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' || {
  echo "  -> VoiceCheck needs Python 3.10+. Upgrade before continuing."
  exit 1
}

if [ -n "${VIRTUAL_ENV:-}" ]; then
  echo "venv: active ($VIRTUAL_ENV)"
else
  echo "venv: none — recommend 'python3 -m venv .venv && source .venv/bin/activate'"
fi

if "$py" -c 'import voicecheck' 2>/dev/null; then
  echo "voicecheck: installed ($("$py" -c 'import voicecheck; print(voicecheck.__version__)' 2>/dev/null))"
else
  echo "voicecheck: not installed — 'pip install \"voicecheck[all]\"'"
fi

if [ -f examples/echo_smoke.yaml ]; then
  echo "repo: inside VoiceCheck repo (examples/ available for the smoke test)"
else
  echo "repo: not in the VoiceCheck repo — use the bundled templates/first_scenario.yaml"
fi
exit 0
