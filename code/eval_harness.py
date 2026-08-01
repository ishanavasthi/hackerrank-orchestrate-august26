#!/usr/bin/env python3
"""M5 — unified evaluation harness.

    python code/eval_harness.py [--provider stub|anthropic|nvidia]

Runs every check we have in one pass and returns a single exit code, so a
regression cannot hide behind a check nobody remembered to run:

    1. contract validation   — output.csv is submittable at all
    2. M2 safety gate        — must-mute holds, trusted senders untouched, blind
    3. M5 edge cases         — the four edge classes + no silent fallbacks
    4. sample smoke test     — accuracy against the 30 labelled rows

On (4): this is a SMOKE TEST, not a fitting target. Per DECISIONS.md we do not
tune thresholds to reproduce these labels, and their 9/11/10 action split is too
uniform to be the real class balance. It is here to catch a collapse, not to be
maximised — so it prints numbers and a drop warning, and does not fail the run
on a small movement.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODE = REPO / "code"

#: Below this, something has broken rather than drifted. Deliberately loose —
#: it is a floor, not a goal.
SMOKE_FLOOR_ACTION = 0.60


def run(label: str, argv: list[str]) -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, *argv], cwd=REPO,
                          capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    ok = proc.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        for line in output.strip().splitlines()[-12:]:
            print(f"        {line}")
    return ok, output


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="stub",
                    choices=["stub", "anthropic", "nvidia"])
    ap.add_argument("--out", default="output.csv")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="Skip the sample smoke test (avoids live calls on an LLM provider).")
    args = ap.parse_args()

    print(f"=== evaluation harness (provider={args.provider}) ===\n")
    results: list[bool] = []

    ok, _ = run("contract validation", [str(CODE / "validate.py"), args.out])
    results.append(ok)

    ok, _ = run("M2 safety gate", [str(CODE / "gate_m2.py")])
    results.append(ok)

    ok, _ = run("M5 edge cases", [str(CODE / "edge_cases.py"), "--out", args.out])
    results.append(ok)

    if args.skip_smoke:
        print("SKIP  sample smoke test")
    else:
        ok, output = run("sample smoke test",
                         [str(CODE / "score_samples.py"), "--provider", args.provider])
        results.append(ok)
        action = re.search(r"action\s*:\s*(\d+)/(\d+)", output)
        mtype = re.search(r"message_type\s*:\s*(\d+)/(\d+)", output)
        if action:
            hit, total = int(action.group(1)), int(action.group(2))
            rate = hit / total
            print(f"        action {hit}/{total} = {rate:.0%}"
                  + (f", message_type {mtype.group(1)}/{mtype.group(2)}" if mtype else ""))
            if rate < SMOKE_FLOOR_ACTION:
                print(f"        WARNING: below the {SMOKE_FLOOR_ACTION:.0%} floor — "
                      "this looks like a break, not drift")
                results.append(False)

    print()
    if all(results):
        print("ALL CHECKS PASS")
        return 0
    print(f"{results.count(False)} CHECK(S) FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
