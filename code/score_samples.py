#!/usr/bin/env python3
"""Self-evaluation against the 30 labelled rows in dataset/sample_messages.csv.

    python code/score_samples.py [--verbose]

README.md explicitly invites this ("Evaluate your approach on the solved
sample rows before submitting"). It is a measurement tool, not a fitting
target — per DECISIONS.md we deliberately do not tune thresholds to reproduce
these labels, and their 9/11/10 action split is too uniform to be the real
class balance. Treat per-row correctness as signal and the distribution as
noise.
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "code"))

from contracts import Message          # noqa: E402
from data import Dataset               # noqa: E402
from personalize import personalize    # noqa: E402
from safety import safety_verdict      # noqa: E402


def _message(row: dict) -> Message:
    return Message(
        message_id=row["message_id"], user_id=row["user_id"],
        conversation_type=row["conversation_type"], group_id=row["group_id"],
        business_id=row["business_id"], sender_user_id=row["sender_user_id"],
        created_at=row["created_at"], message_text=row["message_text"],
        media_type=row["media_type"], media_id=row["media_id"],
        forwarded_count=int(row["forwarded_count"] or 0),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--provider", default="stub", choices=["stub", "anthropic", "nvidia"],
                    help="Personalization engine. The safety gate always uses the "
                         "deterministic rules — see main.py --safety-provider.")
    args = ap.parse_args()

    if args.provider != "stub":
        sys.path.insert(0, str(REPO / "code"))
        from main import load_dotenv       # noqa: PLC0415
        load_dotenv(REPO / ".env")

    ds = Dataset.load(REPO / "dataset", REPO / "code" / "cache" / "media.json")
    rows = list(csv.DictReader(open(REPO / "dataset" / "sample_messages.csv")))

    act_ok = typ_ok = 0
    confusion: collections.Counter = collections.Counter()
    type_confusion: collections.Counter = collections.Counter()
    misses = []

    for row in rows:
        ctx = ds.context_for(_message(row))
        # Safety always runs on rules; only personalization varies by provider.
        verdict = safety_verdict(ctx)
        if verdict.force_mute:
            action, mtype = "mute", verdict.message_type
        elif args.provider == "stub":
            decision = personalize(ctx)
            action, mtype = decision.action, decision.message_type
        else:
            from router import route       # noqa: PLC0415
            decision = route(ctx, provider=args.provider)
            action, mtype = decision.action, decision.message_type

        act_ok += action == row["action"]
        typ_ok += mtype == row["message_type"]
        confusion[(row["action"], action)] += 1
        if mtype != row["message_type"]:
            type_confusion[(row["message_type"], mtype)] += 1
        if action != row["action"]:
            misses.append((row["message_id"], row["action"], action,
                           row["message_type"], mtype, row["message_text"][:60]))

    n = len(rows)
    print(f"=== self-score vs {n} labelled sample rows ===")
    print(f"  action        : {act_ok}/{n} = {act_ok / n:.0%}")
    print(f"  message_type  : {typ_ok}/{n} = {typ_ok / n:.0%}")

    print("\n  action confusion (truth -> predicted):")
    for (truth, pred), count in sorted(confusion.items(), key=lambda kv: -kv[1]):
        print(f"    {truth:7s} -> {pred:7s}  {count}{'' if truth == pred else '   MISS'}")

    if type_confusion:
        print("\n  top message_type misses (truth -> predicted):")
        for (truth, pred), count in sorted(type_confusion.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {truth:16s} -> {pred:16s} {count}")

    if args.verbose and misses:
        print("\n  action misses in detail:")
        for mid, want_a, got_a, want_t, got_t, text in misses:
            print(f"    {mid}: action {want_a} -> {got_a}, type {want_t} -> {got_t}")
            print(f"      {text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
