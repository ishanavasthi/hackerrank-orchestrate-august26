# NEXT.md — ordered work plan

All milestones (M0–M5) are complete. `python code/eval_harness.py --provider nvidia`
is green: contract PASS, M2 gate PASS, M5 edges PASS, smoke 93% action / 83% type.

This file is written to survive conversation compaction. Each item is
self-contained: what, why it matters, how to know it is done. Work top-down —
the ordering is by risk to the submission, not by effort.

State at time of writing: commit `6150e88`, `output.csv` = 110 rows,
`md5 46ce2fa795a50830a854c599b71ebcd9`, shipping path = rules safety gate +
NVIDIA NIM personalization.

---

## P0 — Submission integrity. Do these first; everything else is optional beside them.

### 1. `.env` will leak live API keys into the submission zip
**Severity: critical.** `.env` sits in the working directory holding four live
keys (Groq, Gemini, Anthropic, NVIDIA). It is gitignored, so it never reached
git — but the deliverable is a **code zip**, and `zip -r code.zip .` from the
repo root sweeps it in. AGENTS.md §3.2 rule 4 forbids shipping secrets.

*Fix:* add `make_submission.py` (or a documented `zip -x` invocation) that
builds the bundle from an explicit allowlist, never a directory sweep.

*Done when:* the produced zip is unpacked to a temp dir and a grep for
`sk-ant-|nvapi-|AIza|gsk_` and for a file named `.env` both return nothing.

### 2. No submission bundle has ever been produced or verified
The README requires three artifacts: **code zip**, **`output.csv`**, **chat
transcript** (`~/hackerrank_orchestrate_august26/log.txt`). We have never
assembled or tested them. The transcript itself is healthy — checked: contains
`AGREEMENT RECORDED`, 32 turn entries, 0 secret-shaped strings.

*Fix:* produce the bundle, unpack it into a clean temp directory, and run the
pipeline there end-to-end on the offline path.

*Done when:* `python code/main.py --provider stub` inside the unpacked zip
produces a valid 110-row `output.csv` with no network and no keys.

---

## P1 — The silent-failure class. This is the bug family that has bitten us most.

### 3. Cache invalidation is manual across three caches
`code/cache/{media,routing,safety}` are keyed by `message_id` / `media_id`
only. Edit a prompt and the old decisions replay silently — the same shape as
the M5 bug where an error handler's output shipped as a real judgement, and
the M1 bug where a field-name mismatch silently emptied 13 transcripts.

*Fix:* include a hash of (system prompt + user prompt) in the cache key or as a
stored field checked on read; on mismatch, re-call rather than replay.

*Done when:* changing one character of `SYSTEM_PROMPT` causes a re-call instead
of a cache hit, demonstrated in a test.

---

## P2 — Scoring criteria we have never actually examined

### 4. `reason` quality has never been systematically reviewed
It is **1 of the 5 stated scoring criteria** and we have only ever glanced at
individual examples. There is no ground truth, so this is a manual read, not a
metric.

*Fix:* dump all 110 reasons, read them as a set, and look for: template
repetition, reasons that restate the action without justifying it, reasons
citing evidence we did not actually use, and the 22 gate-forced rows whose
reason is a machine-generated signal list rather than prose.

*Done when:* every reason has been read once and the weak classes are either
fixed or recorded in §7.

### 5. The confidence band compresses our signal (§7 F1)
Internal certainty has real resolution, but the 0.78–0.91 output band is only
0.13 wide, so a deliberate 0.15 penalty moves the number by ~0.01. Currently
7 distinct values across 110 rows.

*Fix:* decide explicitly — either widen the band and accept divergence from the
30-row sample range, or keep it and record that our confidence is
near-constant by construction.

*Done when:* the choice is made and written into `DECISIONS.md`, not left implicit.

---

## P3 — Decisions to finalise rather than leave half-stated

### 6. Rules fallback is much weaker than the shipping path (§7 A2)
70%/47% versus 93%/83%. A no-key run still produces a valid `output.csv`, just a
materially worse one. This is currently an unstated asymmetry.

*Fix:* either state it plainly in `code/README.md` as a degraded-but-valid
fallback, or close part of the gap (the LLM's `message_type` discrimination is
the bigger half).

### 7. "Risk has exactly one owner" is no longer true (§7 A4)
LLM personalization labels `scam` on 7 gate-cleared rows — all group messages
with no business record, where the structural gate is blind. The safety
property still holds end-to-end (0 of 23 trusted senders mislabelled), but the
`DECISIONS.md` entry asserting single ownership is now inaccurate.

*Fix:* rewrite that entry to describe what the system actually does.

---

## P4 — Hygiene. No measurable effect; do only if time remains.

8. **`code/evaluation/main.py`** is an empty committed organizer scaffold (§7 D7) — delete or use it.
9. **`eval_harness.py` does not regenerate the artifact** (§7 F4) — add an optional `--run`.
10. **LLM calls are sequential** (§7 D2); a full run takes several minutes. Bounded concurrency is safe (every call is independent and cached) but only affects re-runs, not the shipped file.

---

## Do NOT reopen

Both were investigated and closed on evidence. Re-litigating them costs time and
would make the output worse.

- **`spam` emission** — resolved-negative. The boundary is sender disrepute
  (unverified + 23 reports), not unwanted marketing; the test set has zero
  triggers on either discriminator. See `DECISIONS.md`.
- **Text/image brand mismatch as a scam signal** — disproved. The dataset
  recycles stock imagery across unrelated senders; `img_010` is labelled
  `mute`/`promotion` under a different sender in the sample set.

---

## Standing verification

Run before and after every change:

```bash
python code/eval_harness.py --provider nvidia    # everything, one exit code
python code/main.py --provider nvidia && md5 -q output.csv   # determinism
```

`CHECKLIST.md` §7 is the full trade-off backlog (28+ items, tagged GAP /
ACCEPTED / BLIND). This file is only the subset that warrants action now.
