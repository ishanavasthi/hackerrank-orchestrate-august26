#!/usr/bin/env python3
"""M2 gate: does the blind safety gate mute what it must, and nothing it must not?

    python code/gate_m2.py [--provider stub|anthropic|nvidia]

Three assertions, per the milestone definition:
  1. msg_091 (OTP phishing from a PERSONAL contact) force-mutes.
  2. Every impersonation-domain business row force-mutes.
  3. No trusted sender is force-muted. "Trusted" = a matching official domain,
     or a verified long-established sender using a link shortener. Those rows
     may still be muted later by personalization as low-value; what must not
     happen is the SAFETY gate branding them scam/spam.

Exit 0 on pass, 1 on failure, printing every failure rather than the first.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "code"))

from data import Dataset            # noqa: E402
from safety import (                # noqa: E402
    FORBIDDEN_ENGAGEMENT_FIELDS, assert_blind, build_safety_context,
    gate_all, render_safety_prompt, structural_risk,
)


def build_expectations() -> tuple[set[str], set[str], dict[str, str]]:
    """Derive the expected sets from the data itself, not a hardcoded list."""
    biz = {b["business_id"]: b for b in csv.DictReader(open(REPO / "dataset" / "business_accounts.csv"))}
    msgs = list(csv.DictReader(open(REPO / "dataset" / "messages.csv")))

    must_mute = {"msg_091"}          # personal-contact OTP phish
    must_not_mute: set[str] = set()
    label: dict[str, str] = {"msg_091": "personal OTP phish"}

    for m in msgs:
        b = biz.get(m["business_id"])
        if not b:
            continue
        official = (b["official_domain"] or "").strip()
        used = (b["domain_used_by_sender"] or "").strip()
        verified = str(b["verified"]).strip() in {"1", "true", "True"}
        age = int(b["account_age_days"] or 0)
        reports = int(b["user_reports_30d"] or 0)
        mid = m["message_id"]

        if not official or official == used:
            must_not_mute.add(mid)
            label[mid] = f"clean domain ({b['display_name']})"
        elif not verified and age < 180:
            must_mute.add(mid)
            label[mid] = f"impersonation ({b['display_name']}, {reports} reports)"
        else:
            # verified + long-established, mismatch is a link shortener
            must_not_mute.add(mid)
            label[mid] = f"shortener, verified {age}d ({b['display_name']})"

    return must_mute, must_not_mute, label


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="stub", choices=["stub", "anthropic", "nvidia"])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    ds = Dataset.load(REPO / "dataset", REPO / "code" / "cache" / "media.json")
    contexts = [ds.context_for(m) for m in ds.messages]
    verdicts = gate_all(contexts, provider=args.provider)
    must_mute, must_not_mute, label = build_expectations()

    failures: list[str] = []

    print(f"=== M2 blind safety gate (provider={args.provider}) ===\n")

    print(f"--- assertion 1+2: must force-mute ({len(must_mute)} rows) ---")
    for mid in sorted(must_mute):
        v = verdicts[mid]
        ok = v.force_mute and v.message_type in {"scam", "spam"}
        print(f"  {'PASS' if ok else 'FAIL'}  {mid}  {label[mid]}")
        if ok and args.verbose:
            print(f"          -> {v.message_type}: {v.reason}")
        if not ok:
            failures.append(f"{mid} ({label[mid]}) was NOT force-muted")

    print(f"\n--- assertion 3: must NOT be force-muted ({len(must_not_mute)} rows) ---")
    false_positives = []
    for mid in sorted(must_not_mute):
        v = verdicts[mid]
        if v.force_mute:
            false_positives.append(mid)
            print(f"  FAIL  {mid}  {label[mid]}  -> falsely muted as {v.message_type}: {v.reason}")
            failures.append(f"{mid} ({label[mid]}) was falsely force-muted")
    if not false_positives:
        print(f"  PASS  no false positives across {len(must_not_mute)} trusted rows")

    # Assertion 4 (structural, not in the brief but load-bearing): the gate
    # must be provably blind, not just intended to be.
    print("\n--- assertion 4: blindness is enforced ---")
    leaks = []
    for ctx in contexts:
        s = build_safety_context(ctx)
        try:
            assert_blind(render_safety_prompt(s))
        except AssertionError as exc:
            leaks.append(str(exc))
    sc_fields = set(build_safety_context(contexts[0]).__dataclass_fields__)
    overlap = sc_fields & FORBIDDEN_ENGAGEMENT_FIELDS
    if leaks:
        failures.extend(leaks[:3])
        print(f"  FAIL  {len(leaks)} prompts leaked engagement fields")
    elif overlap:
        failures.append(f"SafetyContext exposes engagement fields: {sorted(overlap)}")
        print(f"  FAIL  SafetyContext exposes {sorted(overlap)}")
    else:
        print(f"  PASS  no engagement field reaches the gate "
              f"({len(FORBIDDEN_ENGAGEMENT_FIELDS)} names checked over {len(contexts)} prompts)")

    total_muted = sum(1 for v in verdicts.values() if v.force_mute)
    print(f"\n--- summary ---")
    print(f"  force-muted by gate: {total_muted}/{len(verdicts)}")
    print(f"  (the remaining {len(verdicts) - total_muted} pass through to personalization)")

    if failures:
        print(f"\nFAIL — {len(failures)} problem(s):")
        for f in failures:
            print(f"  * {f}")
        return 1
    print("\nPASS — all M2 gate assertions hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
