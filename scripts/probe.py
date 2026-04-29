#!/usr/bin/env python3
"""scripts/probe.py — CLI shim for Topic Probe (Tier 1, v0.4.12).

Translates a hardcoded preset (lifted from PROMPT_SETS in
validate_taxonomy_emergence.py) into a POST /api/probes request.

Usage:
    python scripts/probe.py <preset-name>

Backend MUST be running on localhost:8000 (override via PROBE_API_BASE env var).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
API_BASE = os.environ.get("PROBE_API_BASE", "http://localhost:8000")


def _load_prompt_sets() -> dict[str, list[str]]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from validate_taxonomy_emergence import PROMPT_SETS  # type: ignore

    return PROMPT_SETS


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python scripts/probe.py <preset-name>", file=sys.stderr)
        return 2

    preset_name = argv[1]
    presets = _load_prompt_sets()
    if preset_name not in presets:
        print(
            f"Unknown preset '{preset_name}'. Known: {list(presets)}",
            file=sys.stderr,
        )
        return 2

    prompts = presets[preset_name]
    print(f"Probe preset: {preset_name} ({len(prompts)} prompts)")

    body = {
        "topic": preset_name,
        "n_prompts": len(prompts),
        "repo_full_name": "project-synthesis/ProjectSynthesis",
        "intent_hint": "audit",
    }

    try:
        # Timeout calibration (v0.4.12, 2026-04-29):
        #   Each prompt = full pipeline ~354s median, max ~491s.
        #   Probe runs N prompts (5-25). 3600s (1 hour) covers a
        #   10-prompt probe with headroom; longer probes need Tier 2's
        #   202 Accepted + polling architecture.
        with httpx.Client(timeout=3600.0) as client:
            with client.stream("POST", f"{API_BASE}/api/probes", json=body) as resp:
                count = 0
                total = body["n_prompts"]
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if "current" in data and "intent_label" in data:
                        count = data.get("current", count + 1)
                        print(
                            f"[{count}/{total}] "
                            f"{data.get('intent_label', '?')} "
                            f"overall={data.get('overall_score', 0.0):.2f}"
                        )
                    elif "final_report" in data:
                        print("\n" + (data.get("final_report") or ""))
    except httpx.ConnectError as exc:
        print(
            f"Could not reach backend at {API_BASE}: {exc}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
