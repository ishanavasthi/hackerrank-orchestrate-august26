#!/usr/bin/env python3
"""M5 — edge-case gate.

    python code/edge_cases.py [--out output.csv]

Asserts the four edge classes named in the milestone behave sanely, plus one
guard that generalises beyond them.

Edge cases are where a pipeline degrades quietly rather than loudly: nothing
raises, `output.csv` still validates, and the row is simply wrong. The
silent-fallback assertion below exists because exactly that happened — the
model's reply for msg_056 was truncated mid-JSON, extraction returned nothing,
and a constant `digest`/`unknown` was substituted. The spec's own carve-out
example was being answered by an error handler, and every contract check still
reported PASS.

Exit 0 on pass, 1 on failure, printing every failure rather than the first.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "code"))

from data import Dataset                                    # noqa: E402
from personalize import signals_for                         # noqa: E402
from safety import (                                        # noqa: E402
    build_safety_context, content_risk, structural_risk,
)

#: Substrings that mean a decision came from an error path rather than a
#: judgement. Any of these in a shipped `reason` is a silent failure.
FALLBACK_MARKERS = (
    "not valid json",
    "safe fallback",
    "could not parse",
    "unavailable",
)


def load(out_path: Path):
    ds = Dataset.load(REPO / "dataset", REPO / "code" / "cache" / "media.json")
    rows = {r["message_id"]: r for r in csv.DictReader(open(out_path))}
    users = {u["user_id"] for u in csv.DictReader(open(REPO / "dataset" / "users.csv"))}
    return ds, rows, users


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "output.csv")
    args = ap.parse_args()

    ds, rows, users = load(args.out)
    failures: list[str] = []
    print(f"=== M5 edge-case gate ({args.out.name}) ===\n")

    # ── 0. No decision may come from an error path ──────────────────────────
    silent = [(mid, r["reason"][:70]) for mid, r in rows.items()
              if any(marker in r["reason"].lower() for marker in FALLBACK_MARKERS)]
    print("--- 0: no silently-degraded decisions ---")
    if silent:
        for mid, reason in silent:
            print(f"  FAIL  {mid}: {reason}")
            failures.append(f"{mid} was decided by an error fallback, not a judgement")
    else:
        print(f"  PASS  no fallback markers across {len(rows)} rows")

    # ── 1. Unknown senders ──────────────────────────────────────────────────
    print("\n--- 1: unknown senders ---")
    unknown = []
    for m in ds.messages:
        if m.conversation_type != "personal":
            continue
        ctx = ds.context_for(m)
        prior = [h for h in ctx.history if h.get("sender_user_id") == m.sender_user_id]
        if m.sender_user_id not in users or not prior:
            unknown.append(m.message_id)
    for mid in unknown:
        row = rows[mid]
        # An unknown sender is a reason for caution, never on its own a reason
        # to call something a scam. Risk must come from content or structure.
        if row["message_type"] in {"scam", "spam"}:
            ctx = ds.context_for(next(m for m in ds.messages if m.message_id == mid))
            risky, _ = content_risk(build_safety_context(ctx))
            if not risky:
                print(f"  FAIL  {mid} labelled {row['message_type']} with no content risk")
                failures.append(f"{mid}: unknown sender treated as risk without evidence")
    print(f"  PASS  {len(unknown)} unknown-sender rows, none labelled risky without cause"
          if not failures or all("unknown sender" not in f for f in failures) else "  (see above)")

    # ── 2. Thin or empty history ────────────────────────────────────────────
    print("\n--- 2: thin/empty history ---")
    thin = [m.message_id for m in ds.messages if len(ds.context_for(m).history) <= 5]
    bad_thin = [mid for mid in thin if not rows[mid]["reason"].strip()
                or rows[mid]["action"] not in {"notify", "digest", "mute"}]
    if bad_thin:
        for mid in bad_thin:
            print(f"  FAIL  {mid} produced an unusable decision on thin history")
            failures.append(f"{mid}: thin history produced an unusable decision")
    else:
        print(f"  PASS  {len(thin)} rows with <=5 history rows all decided cleanly")
        print(f"        (evidence may legitimately be 'none' here: "
              f"{sum(1 for mid in thin if rows[mid]['evidence_message_ids'] == 'none')}/{len(thin)})")

    # ── 3. Muted group with an urgent direct mention (the spec's carve-out) ─
    print("\n--- 3: muted group + urgent direct mention ---")
    carve = []
    for m in ds.messages:
        ctx = ds.context_for(m)
        s = signals_for(ctx)
        if s.group_muted and s.direct_mention:
            carve.append((m.message_id, s.really_urgent, rows[m.message_id]["action"]))
    for mid, urgent, action in carve:
        if urgent and action != "notify":
            print(f"  FAIL  {mid} is an urgent direct mention in a muted group but got {action!r}")
            failures.append(f"{mid}: spec carve-out not honoured (got {action}, want notify)")
        elif not urgent and action == "notify":
            print(f"  FAIL  {mid} is a non-urgent mention in a muted group but got notify")
            failures.append(f"{mid}: mention alone should not override a group mute")
        else:
            kind = "urgent -> notify" if urgent else "not urgent -> suppressed"
            print(f"  PASS  {mid} ({kind}, got {action})")
    if not carve:
        print("  WARN  no muted-group mentions found — assertion vacuous")

    # ── 4. Borderline scam ──────────────────────────────────────────────────
    # Rows where structural and content risk disagree. Either signal alone is
    # enough to mute, so what we check is that the decision is consistent with
    # the gate rather than half-applied.
    print("\n--- 4: borderline scam (one risk signal only) ---")
    borderline = []
    for m in ds.messages:
        ctx = ds.context_for(m)
        sc = build_safety_context(ctx)
        st = structural_risk(sc)
        risky, _ = content_risk(sc)
        if st.impersonation != risky:
            borderline.append((m.message_id, "structural" if st.impersonation else "content",
                               rows[m.message_id]["action"], rows[m.message_id]["message_type"]))
    inconsistent = [b for b in borderline if b[2] != "mute"]
    for mid, kind, action, mtype in inconsistent:
        print(f"  FAIL  {mid} has {kind} risk but action={action}")
        failures.append(f"{mid}: {kind} risk present but not muted")
    if not inconsistent:
        s_only = sum(1 for b in borderline if b[1] == "structural")
        print(f"  PASS  {len(borderline)} single-signal rows all muted "
              f"({s_only} structural-only, {len(borderline)-s_only} content-only)")

    print("\n--- summary ---")
    print(f"  unknown senders {len(unknown)} | thin history {len(thin)} | "
          f"carve-out {len(carve)} | borderline {len(borderline)}")
    if failures:
        print(f"\nFAIL — {len(failures)} problem(s):")
        for f in failures:
            print(f"  * {f}")
        return 1
    print("\nPASS — all M5 edge-case assertions hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
