#!/usr/bin/env python3
"""Report which environment variables a VoiceCheck transport needs and whether
they are set. Never prints secret values — only SET / MISSING.

Usage:
    python3 check_env.py <transport>     # livekit | daily | vapi | retell
    python3 check_env.py livekit --token-server   # LiveKit token-server mode

Exit 0 if all required vars are set, 1 otherwise.
"""

from __future__ import annotations

import os
import sys

# Var names mirror the repo's .env.example. "Judge" keys are listed separately
# because they're optional unless you use openai audio / llm_judge / persona mode.
REQUIRED = {
    "livekit": ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICECHECK_AGENT_NAME"],
    "livekit-token-server": [
        "TOKEN_SERVER_URL",
        "FIREBASE_TEST_TOKEN",
        "TEST_KID_ID",
        "TEST_AGENT_ID",
    ],
    "daily": ["DAILY_API_KEY"],
    "vapi": ["VAPI_API_KEY", "VAPI_ASSISTANT_ID"],
    "retell": ["RETELL_API_KEY", "RETELL_AGENT_ID"],
    "echo": [],
}
JUDGE_OPTIONAL = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    if not args:
        print(__doc__)
        return 2
    transport = args[0].lower()
    if transport == "livekit" and ("--token-server" in flags or "--token_server" in flags):
        transport = "livekit-token-server"
    if transport not in REQUIRED:
        print(f"Unknown transport '{transport}'. Choose: {', '.join(sorted(REQUIRED))}")
        return 2

    required = REQUIRED[transport]
    print(f"Transport: {transport}")
    if not required:
        print("  (no environment variables required)")
    missing = []
    for name in required:
        ok = bool(os.environ.get(name))
        print(f"  {'SET    ' if ok else 'MISSING'}  {name}")
        if not ok:
            missing.append(name)

    judge_set = [n for n in JUDGE_OPTIONAL if os.environ.get(n)]
    print(
        "LLM key (needed for openai audio / llm_judge / persona): "
        + (", ".join(judge_set) + " SET" if judge_set else "none set (fine for edge+whisper)")
    )

    if missing:
        print(f"\nMissing {len(missing)}: {', '.join(missing)}")
        print("Set them in a local .env (gitignored) or export in your shell.")
        return 1
    print("\nAll required variables are set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
