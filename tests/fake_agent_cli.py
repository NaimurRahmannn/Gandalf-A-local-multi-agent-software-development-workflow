"""Deterministic stand-in used to exercise real subprocess adapters."""

from __future__ import annotations

import sys


def main() -> int:
    agent = sys.argv[1]
    prompt = sys.stdin.read() if not sys.stdin.isatty() else " ".join(sys.argv[2:])
    if not prompt:
        prompt = " ".join(sys.argv[2:])
    output = f"# {agent} output\n\nReceived {len(prompt)} prompt characters."
    if agent == "antigravity" and "REVIEW_DECISION" in prompt:
        output += "\n\nREVIEW_DECISION: APPROVED"
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
