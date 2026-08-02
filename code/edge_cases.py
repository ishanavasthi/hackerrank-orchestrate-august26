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
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "code"))

from contracts import Decision                              # noqa: E402
from data import Dataset                                    # noqa: E402
from personalize import (                                   # noqa: E402
    Signals, enforce_promotion_policy, personalize, signals_for,
)
from router import (                                        # noqa: E402
    DEGRADED_REASON_MARKERS, DEGRADED_REASON_SAMPLES,
    _apply_m4, _load_cache, _validate_decision, route_stub,
)
from safety import (                                        # noqa: E402
    build_safety_context, content_risk, structural_risk,
)

#: Substrings that mean a decision came from an error path rather than a
#: judgement. Any of these in a shipped `reason` is a silent failure.
#:
#: IMPORTED from router.py rather than restated here. This list used to be a
#: local copy, and it drifted: it matched one of the four reasons router.py can
#: actually emit and was blind to the other three — including
#: `[rules fallback: model output unreadable]`, added by the very commit that
#: fixed the original silent-fallback incident. A guard that has drifted from
#: the thing it guards is worse than no guard, because it reports PASS.
FALLBACK_MARKERS = DEGRADED_REASON_MARKERS + (
    # Defensive extras: nothing emits these today, but they are specific enough
    # that a legitimate reason is very unlikely to contain them.
    "could not parse",
    "error handler",
)


def _degraded(reason: str) -> bool:
    return any(marker in reason.lower() for marker in FALLBACK_MARKERS)


def self_test_guard() -> list[str]:
    """Prove the guard catches every degraded reason router.py can produce.

    Without this, the marker list is an assumption. With it, adding a
    degradation path whose marker was forgotten fails the gate immediately
    instead of silently widening the blind spot.
    """
    missed = [s for s in DEGRADED_REASON_SAMPLES if not _degraded(s)]
    # A guard that fires on ordinary prose would be worse than useless.
    false_positives = [s for s in (
        "A trusted group admin sent a time-sensitive update.",
        "The item the sender mentions is currently unavailable in store.",
        "The user opened similar banking updates before.",
    ) if _degraded(s)]
    return ([f"guard does not catch: {s!r}" for s in missed]
            + [f"guard false-positives on: {s!r}" for s in false_positives])


def self_test_promotion_policy() -> list[str]:
    """Prove `enforce_promotion_policy` does what section 5 relies on it doing.

    Same shape as `self_test_guard` above and for the same reason: checking the
    artifact with an unverified guard is how assertion 0 came to report PASS
    while blind to three of the four degradation paths.
    """
    problems: list[str] = []

    def _d(action: str, mtype: str) -> Decision:
        return Decision(message_id="selftest", action=action, message_type=mtype,
                        reason="time-sensitive and expects a response now",
                        confidence=0.9, evidence_message_ids=[])

    # The extra step to `mute` is taken on CONTENT (`s.promo`), not on the type
    # label, because classify_type falls through to `promotion` for any business
    # message with no transactional word. The last two cases are msg_075 and
    # msg_049: mistyped transactional rows that must be deferred, not suppressed.
    cases = [
        ("promotions opted out", Signals(promo=True, promo_unwanted=True), "mute"),
        ("dormant relationship", Signals(promo=True, relationship_stale=True), "mute"),
        ("promotions accepted",  Signals(promo=True), "digest"),
        ("mistyped, opted out",  Signals(promo_unwanted=True), "digest"),
        ("mistyped, dormant",    Signals(relationship_stale=True), "digest"),
    ]
    for label, signals, want in cases:
        d = enforce_promotion_policy(_d("notify", "promotion"), signals)
        if d.action != want:
            problems.append(f"notify/promotion + {label} -> {d.action!r}, want {want!r}")
        if _degraded(d.reason):
            problems.append(f"demotion reason for {label} trips the fallback guard")
        # The reason must argue for the action actually emitted.
        if "interrupt" not in d.reason.lower():
            problems.append(f"demotion reason for {label} does not state the rule")

    # It must not touch anything else: not a non-promotion notify, and not a
    # promotion that is already suppressed.
    untouched = [
        ("notify", "business_update"), ("notify", "urgent"), ("notify", "payment"),
        ("digest", "promotion"), ("mute", "promotion"),
    ]
    for action, mtype in untouched:
        d = enforce_promotion_policy(_d(action, mtype), Signals(promo_unwanted=True))
        if (d.action, d.message_type) != (action, mtype):
            problems.append(f"policy moved {action}/{mtype} -> {d.action}/{d.message_type}")

    return problems


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
    print("--- 0: no silently-degraded decisions ---")

    # 0a. First verify the guard itself. Checking the artifact with a guard we
    # have not checked is how this assertion came to report PASS while blind to
    # three of the four degradation paths.
    guard_problems = self_test_guard()
    if guard_problems:
        for p in guard_problems:
            print(f"  FAIL  {p}")
            failures.append(f"fallback guard is unsound: {p}")
    else:
        print(f"  PASS  guard self-test: catches all {len(DEGRADED_REASON_SAMPLES)} "
              f"degraded reasons router.py can emit, no false positives")

    # 0b. Then the artifact.
    silent = [(mid, r["reason"][:70]) for mid, r in rows.items() if _degraded(r["reason"])]
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

    # ── 5. A promotion never interrupts ─────────────────────────────────────
    # A hard PRODUCT invariant, not a heuristic read off the data: no emitted
    # row may pair action=notify with message_type=promotion. Promotions belong
    # in the digest, or in mute when the user does not want them from this
    # sender. See personalize.enforce_promotion_policy.
    #
    # Checked in four places because the artifact alone would only ever prove it
    # for the provider that produced output.csv, and the pairing is one model
    # token away on the other paths — msg_094 reached notify/promotion through
    # the rules engine with no affinity override involved at all.
    print("\n--- 5: promotions never notify ---")

    # 5a. The rule itself.
    policy_problems = self_test_promotion_policy()
    if policy_problems:
        for p in policy_problems:
            print(f"  FAIL  {p}")
            failures.append(f"promotion policy is unsound: {p}")
    else:
        print("  PASS  policy self-test: notify/promotion demotes to mute when the "
              "user rejects promotions, digest otherwise, and nothing else moves")

    # 5b. The artifact.
    artifact_bad = sorted(mid for mid, r in rows.items()
                          if r["action"] == "notify" and r["message_type"] == "promotion")
    for mid in artifact_bad:
        print(f"  FAIL  {mid} is notify/promotion in {args.out.name}")
        failures.append(f"{mid}: promotion routed to notify")
    if not artifact_bad:
        n_promo = sum(1 for r in rows.values() if r["message_type"] == "promotion")
        print(f"  PASS  {n_promo} promotion rows in {args.out.name}, none notify")

    # 5c. The live decision paths, offline. `personalize()` is what
    #     --provider stub emits and is also the LLM rules-fallback; `route_stub`
    #     is the crude classifier. Neither may produce the pairing on any row.
    live_bad = []
    for m in ds.messages:
        ctx = ds.context_for(m)
        for label, d in (("rules", personalize(ctx)), ("stub-classifier", route_stub(ctx))):
            if d.action == "notify" and d.message_type == "promotion":
                live_bad.append((m.message_id, label))

    # 5d. The LLM path, replayed from cache — no network, and it exercises the
    #     same _apply_m4 a live run would. Skipped silently for a provider with
    #     no cache.
    replayed = 0
    for provider in ("anthropic", "nvidia"):
        for m in ds.messages:
            cached = _load_cache(provider, m.message_id)
            if cached is None:
                continue
            replayed += 1
            d = _apply_m4(ds.context_for(m), _validate_decision(cached, m.message_id))
            if d.action == "notify" and d.message_type == "promotion":
                live_bad.append((m.message_id, f"{provider} (cached)"))

    for mid, label in live_bad:
        print(f"  FAIL  {mid} is notify/promotion on the {label} path")
        failures.append(f"{mid}: promotion routed to notify ({label})")
    if not live_bad:
        print(f"  PASS  {len(ds.messages)} rows x rules + stub-classifier + "
              f"{replayed} cached LLM decisions, no notify/promotion")

    # 5e. Checks 3 and 5 must be JOINTLY satisfiable. They were not: an urgent
    #     direct mention worded with a marketing verb typed `promotion` (the
    #     `s.promo` test ran before urgency), took the carve-out branch to
    #     `notify`, and was then demoted straight back out of the carve-out
    #     check 3 requires. Vacuous on today's data — msg_056, the spec's own
    #     example, is one word away from this wording — so it is probed rather
    #     than waited for. Fixed in the TYPING: a message that names the
    #     recipient AND is genuinely time-sensitive is not a promotion.
    base = next(m for m in ds.messages if signals_for(ds.context_for(m)).group_muted
                and m.conversation_type == "group")
    probe = replace(
        base, message_id="probe_carveout", forwarded_count=0,
        message_text=f"@{base.user_id} urgent: book now for tomorrow's trip, "
                     f"the deadline is 6pm today.")
    pctx = ds.context_for(probe)
    ps = signals_for(pctx)
    pd = personalize(pctx)
    if not (ps.direct_mention and ps.really_urgent and ps.promo):
        print("  WARN  carve-out probe no longer exercises the collision "
              f"(mention={ps.direct_mention} urgent={ps.really_urgent} promo={ps.promo})")
    elif pd.action != "notify" or pd.message_type == "promotion":
        print(f"  FAIL  promo-worded urgent direct mention in a muted group got "
              f"{pd.action}/{pd.message_type}; checks 3 and 5 are unsatisfiable together")
        failures.append("carve-out and promotion invariant collide on a "
                        "promo-worded urgent direct mention")
    else:
        print(f"  PASS  promo-worded urgent direct mention -> {pd.action}/"
              f"{pd.message_type}: carve-out honoured, invariant intact")

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
