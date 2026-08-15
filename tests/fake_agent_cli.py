"""Deterministic stand-in used to exercise real subprocess adapters."""

from __future__ import annotations

import sys


def main() -> int:
    agent = sys.argv[1]
    prompt = sys.stdin.read() if not sys.stdin.isatty() else " ".join(sys.argv[2:])
    if not prompt:
        prompt = " ".join(sys.argv[2:])
    print(f"# {agent} output\n\nReceived {len(prompt)} prompt characters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
